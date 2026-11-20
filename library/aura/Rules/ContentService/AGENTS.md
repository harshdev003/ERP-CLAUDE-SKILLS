# AGENTS.md

This file provides guidance to Codex when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
npm run dev          # Start dev server (NODE_ENV=development)
npm run serve        # Start production server
npm run lint         # Run ESLint
npm run lint:cache   # Run ESLint with caching
npm test             # Run Jest tests (excludes e2e, runs serially with --runInBand)
npm run test:e2e     # Run end-to-end tests
```

To run a single test file:
```bash
npx jest test/unit/path/to/test.js
npx jest --testNamePattern="test name"
```

Background jobs are run via `npm run job:<name>` (see package.json for full list, e.g. `job:cache:contents`, `job:algolia:sync`).

## Architecture

Node.js 24 / Express 5 microservice for content management and recommendations in the Aura Health ecosystem. Port 3001.

### Layer Structure

**`src/http/`** — Express HTTP layer
- `handlers/` — Route handlers organized by resource (contents, coaches, programs, channels, etc.)
- `middlewares/` — Request context injection, auth, error handling, logging, compression
- `router.js` — Central route registration

**`src/` (business logic)**
- `models/` — 30+ domain models (Content, Coach, Program, Channel, Series, etc.)
- `builders/` — Factory-style constructors for complex domain objects
- `recommenders/` — Recommendation engines per content type, each with subdirectories for algorithm variants
- `filters/` — Content filtering logic applied to recommendation pipelines
- `parsers/` — Data transformation/normalization utilities
- `stores/` — In-memory cache managers (ContentStore, CoachStore, ButtonStore, etc.) populated by background jobs

**`src/services/`** — All external integrations initialized centrally:
- Firebase (realtime DB + auth), PostgreSQL, Redis (ioredis)
- Algolia (search), BigQuery (analytics), OpenAI (embeddings/moderation)
- Google Cloud: Pub/Sub, Speech, Vertex AI
- Slack (notifications), Lokalise (i18n), Tinify/Remove.bg (images)

**`src/jobs/`** — Background job runners extending a base `Job` class. Jobs initialize all services via `Context` on startup.

**`src/context.js`** — Request-scoped context object carrying transaction IDs, user IDs, service handles, and error tracking. Passed through the entire call stack rather than using globals.

**`src/logger.js`** — Winston logger with Stackdriver JSON format. Use `context.logger` rather than `console`.

**`src/config.js`** — All environment variables centralized here. Never read `process.env` directly elsewhere.

### Key Patterns

- **Context object** — Every handler and service call receives a `context` object. Propagate it everywhere instead of importing services directly.
- **Store pattern** — `stores/` hold preloaded in-memory data refreshed by scheduled jobs. HTTP handlers read from stores, not the DB, for hot-path data.
- **Recommender pipeline** — Each recommender fetches candidates, applies a chain of `filters/`, then ranks/sorts results.
- **Builder pattern** — Complex response objects (e.g., content cards with coach data, program metadata) are assembled in `builders/` from raw model data.

### Database

Schema lives in `database/` (create.sql, update.sql, migrate.sql). PostgreSQL is the system of record; Firebase holds realtime config and category trees; Redis is ephemeral cache.

### Testing

- Tests live in `test/unit/`, `test/integration/`, `test/e2e/`
- Mock data in `test/mocks/`
- Jest timeout: 30 seconds
- Integration tests hit real DB/services; unit tests use mocks from `test/mocks/`
