# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
nvm use                  # Required before development (Node 24.15.0)
npm run dev              # Development server
npm run lint             # ESLint check — run after every code change
npm test                 # Jest test suite
npx jest path/to/test    # Run a single test file
docker-compose up --build  # Start local PostgreSQL database
```

**Background jobs** (run manually or via scheduler):
```bash
npm run job:syncher       # Batch sync users to Iterable
npm run job:resyncher     # Delete & re-sync users
npm run job:unsubscriber  # Process email unsubscriptions
npm run job:alert         # Data alerts
npm run job:report        # Data reports
npm run job:deleteByEmail # Delete users by email
npm run job:setIterableUserIds # Backfill $iterable_user_id on Mixpanel profiles
```

**Local Pub/Sub emulator:**
```bash
gcloud beta emulators pubsub start --project aura-local
export PUBSUB_EMULATOR_HOST=localhost:8085
```

## Architecture

This service manages user data synchronization and event delivery to [Iterable](https://iterable.com) for two projects: **Aura** and **Aurie**. It has HTTP endpoints, background jobs, Pub/Sub listeners, and background workers.

### Layer Structure

```
HTTP/Pub/Sub → Handlers/Listeners → Models → Services → Iterable API
                                           ↓
                                      PostgreSQL + Redis
```

- **`src/http/`** — Express routes, handlers, middlewares. Thin controllers: handlers extract params and delegate entirely to models.
- **`src/models/`** — All business logic lives here. Models add errors to context; the handler checks `context.error` once at the end.
- **`src/services/`** — External API integrations (Iterable, Postgres, Pub/Sub, Slack, Mixpanel, Branch, etc.).
- **`src/jobs/`** — Scheduled batch processing (extend `job.js` base class).
- **`src/listeners/`** — Pub/Sub event listeners. `src/listeners/commands/` contains 100+ individual command handlers.
- **`src/workers/`** — Async background workers (events, experiments, payments).
- **`src/config.js`** — All configuration. Destructure constants at module top, never access `process.env` directly.
- **`src/errors.js`** — All error key definitions.
- **`src/context.js`** — Request context manager passed through the call chain.

### Multi-Project Support

The service supports two Iterable projects (Aura and Aurie) with independent API keys, JWT secrets, push campaigns, and email channels. Configuration distinguishes between production and sandbox environments for each.

### Database Schema

- **`database/create.sql`** — Full schema for fresh installs.
- **`database/update.sql`** — Incremental changes appended at the bottom (never rewrite; only append).
- Use double-quoted identifiers, `TIMESTAMPTZ` with UTC defaults, and indexes on all foreign keys and business IDs.

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

## Key Conventions

**Code style:** Single quotes, trailing commas, 2-space indentation, async/await preferred, guard clauses to reduce nesting.

**Method naming:** Models use `create`, `retrieve`, `update`, `delete`, `list`. For reserved words, use descriptive names (e.g., `deleteJournal`, not `delete`).

**Error handling:** Models catch errors and attach them to context (`context.error = ...`). Handlers check `context.error` exactly once after the model call — never catch and re-throw in multiple places.

**Logging:** Use `logger` (never `console.log`). Include context metadata (user ID, request ID). Levels: `debug` (steps), `info` (milestones), `warn` (potential issues), `error` (failures), `crit` (critical failures).

**Config:** Destructure config values at the top of each module as named constants. All secrets and service URLs must come from environment variables — see `.env.sample` for the full list. Update README.md when adding new config keys.

**Routers:** Every router must include `/ready` and `/status` health check endpoints.

**SQL:** Always use parameterized queries. Never interpolate user input into SQL strings.

**Mixpanel profile writes:** go through `services/mixpanel.js` (`mixpanel-node`), which sends `ip=0` on every request so Mixpanel never re-derives a profile's `$city` / `$region` / `$country_code` / `$timezone` from *our* IP. Never hand-roll a `POST https://api.mixpanel.com/engage` for a bulk fix — if you must, it carries `?ip=0` (or `$ip: 0` per record) and `$ignore_time: true`. On 2026-09-11 the `$iterable_user_id` backfill was re-run as a raw `/engage` script from a laptop without `ip=0`, after `job:setIterableUserIds` was misread as not applying; 2,063,384 profiles were re-stamped West Vancouver, BC (PROD-5908 → PROD-6322). If a bulk write "doesn't land" on read-back, suspect the read first: ID-merged profiles are canonical on `$device:` ids, and engage queries by Firebase UID lag or miss.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
