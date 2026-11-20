# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
npm run dev          # Start Express server in development mode
npm run serve        # Start production server (increased memory)
npm test             # Run all Jest tests (--runInBand)
npm run lint         # Run ESLint
npm run lint:cache   # Run ESLint with cache

# Run a specific test file
NODE_ENV=test npx jest ./test/integration/feeds/ready.spec.js --runInBand

# Background jobs (run independently of the HTTP server)
npm run job:activation
npm run job:alert
npm run job:community
npm run job:feedbackup
npm run job:localize
npm run job:report
npm run job:retention
npm run job:social
```

## Architecture

This is a Node.js/Express microservice that manages user feeds and campaign delivery for the Aura Health platform.

**Startup sequence** (`src/index.js`): Cache → Postgres → Mixpanel → PubSub → Slack → Listeners → HTTP server.

**Layering** (strictly enforced):
- `src/http/handlers/` — Thin HTTP controllers only. Extract params, call models, check `context.error`, respond. No business logic.
- `src/models/` — All business logic lives here. Errors added via `context.addError(key, data)` with keys defined in `src/errors.js`.
- `src/services/` — External integrations: Postgres, Redis, Pub/Sub, OpenAI, Lokalise, Slack, Mixpanel, plus service clients (content, coach, experiment, user).
- `src/listeners/` — Pub/Sub event listeners and ~86 command handlers for event-driven processing.
- `src/campaigns/` — Campaign execution logic (activation, retention, alerts, social, etc.).
- `src/jobs/` — Standalone background job scripts (not part of the HTTP server process).

**Key models**: `src/models/feed.js` (primary), `src/models/campaign.js`, `src/models/localization.js`.

## Code Conventions

**Naming**: `camelCase.js` files, `camelCase` functions, `UPPER_SNAKE_CASE` constants, `PascalCase` classes.

**CRUD method names**: `create`, `retrieve`, `list`, `update`, `remove`, `delete`. REST actions: `assign`, `recommend`, `search`.

**Error handling pattern**:
```js
// In models:
context.addError('ERROR_KEY', { ...data });
// In handlers — single check:
if (context.error) return res.status(422).json(context.error);
```
All error keys must exist in `src/errors.js`.

**Logging**: Use the `logger` service (never `console`). Always pass metadata with `component` and `method`. Levels: `debug`, `info`, `warn`, `error`, `crit`.

**Config**: Destructure from `src/config.js`. Environment variables for secrets/URLs; constants in config for business logic values. When adding a new env var, update `README.md`.

## Database

- `database/create.sql` — Full schema for fresh installs
- `database/update.sql` — Incremental migrations appended at the bottom

Schema conventions: double-quoted identifiers, `TIMESTAMPTZ` for all timestamps, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for migrations, indexes on foreign keys and frequently filtered columns.

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

**Campaign SQL.** Campaigns and jobs that build their own queries use the `sql` template tag in `src/utils/sql.js` (identical in Feed, Email and Notification): every value in `` sql`...${value}...` `` is bound by `.toQuery()`. Use `sql.identifier()` for a column name, and `sql.raw()` only for SQL an admin stored with a campaign (`recipe.users.sql`).

## Testing

Tests live in `test/unit/` and `test/integration/`. Mocks and setup utilities are in `test/setup/` and `test/mocks/`.

Integration tests spin up the Express app via `test/setup/express.js`. Run tests sequentially (`--runInBand`) — this is enforced in the npm script.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
