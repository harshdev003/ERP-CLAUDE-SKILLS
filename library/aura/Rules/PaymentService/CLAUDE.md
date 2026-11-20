# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
nvm use                      # Switch to correct Node.js version (always do this first)
npm run lint                 # Run ESLint (required after every code change)
npm run test                 # Run all tests (Jest, --runInBand)
NODE_ENV=test jest --runInBand ./test/unit/path/to/test.test.js  # Run a single test file
npm run dev                  # Start service in development mode
```

Background jobs are run individually via `npm run job:<name>` (e.g. `npm run job:stripe:cancellation`). See `package.json` for the full list of ~30 job scripts.

## Architecture

### Request lifecycle

```
HTTP request → middleware (auth, context) → handler → model → service/postgres → response
```

- **`src/http/router/`** — Express routers, one folder per domain (payments, subscriptions, receipts, referrals, codes, etc.)
- **`src/http/handlers/`** — Thin controllers: extract params, call model, check `context.error`, return HTTP response. No business logic.
- **`src/models/`** — All business logic: validate input, query DB, call external services, add errors via `context.addError()`.
- **`src/services/`** — Wrappers for external integrations: `postgres.js`, `stripe.js`, `apple.js`, `braintree.js`, `firebase.js`, `pubsub.js`, `slack.js`, etc.
- **`src/jobs/`** — Background job implementations (30+), run via dedicated npm scripts.
- **`src/listeners/`** — Google Cloud Pub/Sub message listeners.

### Context pattern

Every request gets a `Context` instance (see `src/context.js`) injected as `request.context`. It carries:
- `userId`, `token`, transaction ID
- `addError(key, data)` — sets `context.error` using keys from `src/errors.js`
- `meta(extra)` — returns structured metadata object for logger calls

Handlers check `context.error` **once** after the model call. Models never throw for business errors — they call `context.addError()` and return `null`.

### Key files

| File | Purpose |
|------|---------|
| `src/index.js` | Entry point; initializes all services in order, starts HTTP server |
| `src/config.js` | All configuration, loaded from env vars |
| `src/errors.js` | 400+ error key definitions with status codes (alphabetically sorted) |
| `src/context.js` | Context class |
| `src/logger.js` | Winston logger setup |
| `database/create.sql` | Full schema for fresh installs |
| `database/update.sql` | Incremental `ALTER TABLE` migrations (append only) |

### Payment providers

The service integrates with Stripe, Apple IAP, Google Play, Braintree, and PayPal. Each has a corresponding service in `src/services/` and model logic in `src/models/`.

## Rules

Detailed coding standards are in `.claude/rules/`. Keep this list in sync when files are added or removed.

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
