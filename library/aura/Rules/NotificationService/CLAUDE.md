# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Development Setup

Always run `nvm use` before any work to ensure the correct Node.js version.

## Commands

```bash
npm run dev          # Start server in development mode
npm run serve        # Start server in production mode
npm run lint         # Run ESLint (must pass before committing)
npm run test         # Run all tests (NODE_ENV=test jest --runInBand ./test)
```

**Run a single test file:**
```bash
NODE_ENV=test npx jest --runInBand ./test/path/to/spec.js
```

**Background jobs** (run manually or via cron):
```bash
npm run job:activation   # Process activation campaigns
npm run job:retention    # Process retention campaigns
npm run job:localize     # Run LLM-based translation
npm run job:status       # Update OneSignal notification statuses
# See package.json for full list
```

**Mandatory workflow:** After any code change, run `npm run lint` and fix all errors before proceeding.

## Architecture

This is a Node.js/Express push notification service that delivers notifications via OneSignal and Apple APNS, driven by Google Pub/Sub events.

### Layer Structure (`src/`)

| Layer | Path | Responsibility |
|-------|------|----------------|
| HTTP | `http/handlers/`, `http/router/` | Thin controllers — extract params, call model, check `context.error`, return response |
| Models | `models/` | All business logic, DB queries, input validation, error context management |
| Services | `services/` | External API clients (OneSignal, APNS, Postgres, Redis, Pub/Sub, OpenAI, etc.) |
| Listeners | `listeners/` | Pub/Sub event ingestion → `listeners/commands/` (91+ event handlers) |
| Campaigns | `campaigns/` | Campaign-type-specific notification logic |
| Jobs | `jobs/` | Background processing scripts run independently |

### Thin Controller Pattern

Handlers check `context.error` exactly once; models do everything else:

```javascript
// Handler — only HTTP concerns
async function retrieve(request, response) {
  const {context} = request;
  const result = await model.retrieve(context.userId, {context});
  if (context.error) return response.status(context.error.status).json({error: context.error});
  return response.status(200).json({result});
}

// Model — all logic, validation, and error signaling
async function retrieve(userId, {context}) {
  if (!userId) { context.addError('MISSING_USER_ID'); return null; }
  const user = await postgres.getOneById(USER_TABLE, userId, {context});
  if (!user) { context.addError('USER_NOT_FOUND', {userId}); return null; }
  return user;
}
```

Do **not** add a null-check after `context.error` — the model already added the error if the result is null.

### Error Keys

All error keys must be defined in `src/errors.js`, sorted alphabetically within their category. Use semantic names based on what the error represents, not which model uses it.

### Event-Driven Flow

Pub/Sub messages → `src/listeners/eventListener.js` → dispatched to one of 91+ command handlers in `src/listeners/commands/` → campaign logic in `src/campaigns/` → notification creation.

### Router Convention

Every router must include:
```javascript
router.get('/ready', ready);
router.get('/status', status);
```

## Database

- **`database/create.sql`** — full schema for fresh installs
- **`database/update.sql`** — append-only incremental migrations; always use `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`
- Double-quote all identifiers (`"user"`, `"createdAt"`)
- Primary key pattern: `"rowid" SERIAL PRIMARY KEY` + business `"id"` TEXT/UUID
- Timestamps: `TIMESTAMPTZ` with `DEFAULT (NOW() AT TIME ZONE 'utc')`

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

**Campaign SQL.** Campaigns and jobs that build their own queries use the `sql` template tag in `src/utils/sql.js` (identical in Feed, Email and Notification): every value in `` sql`...${value}...` `` is bound by `.toQuery()`. Use `sql.identifier()` for a column name, and `sql.raw()` only for SQL an admin stored with a campaign (`recipe.users.sql`).

## Logging

Never use `console.log`. Use the Winston `logger` from `src/utils/logger.js`:

- `logger.debug()` — operational traces (default for handlers/services)
- `logger.info()` — important business events, one per endpoint completion
- `logger.warn()` — potential issues, fallback scenarios
- `logger.error()` — actual errors with `{error: error.message}`
- `logger.crit()` — critical failures requiring immediate attention

Always include a `meta` object with `{component, method, ...relevantIds}`.

## Code Style

- Single quotes, trailing commas, 2-space indentation
- `async/await` over callbacks/promises
- Guard clauses to reduce nesting
- `Set`/`Map` over repeated `array.includes()` for membership checks
- Named parameters for optional arguments in model methods
- Return dates as ISO 8601

## Git Conventions

- Branch prefixes: `feature/`, `bugfix/`, `refactor/`, `quality/`
- Commits: conventional commits format (`feat(scope): description`)

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
