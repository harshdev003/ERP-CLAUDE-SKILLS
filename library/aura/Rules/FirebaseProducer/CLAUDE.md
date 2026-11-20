# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Rules

Keep this list in sync whenever `.claude/rules/` files are added, renamed, or removed.

@.claude/rules/project-rules.md
@.claude/rules/architecture.md
@.claude/rules/coding.md
@.claude/rules/config.md
@.claude/rules/database.md
@.claude/rules/deployment.md
@.claude/rules/documentation.md
@.claude/rules/error-handling.md
@.claude/rules/logging.md
@.claude/rules/naming.md
@.claude/rules/performance.md
@.claude/rules/security.md

## Setup

Run `nvm use` before making any code changes to ensure Node.js 24.15.0 is active.

All npm commands are run from the `functions/` directory.

## Commands

```bash
# Linting (required after any changes)
cd functions && npm run lint

# Deploy
npm run deploy              # All functions
npm run deploy:dev          # Dev environment
npm run deploy:prod         # Prod environment
npm run deploy:<domain>     # Specific domain (author, challenge, coach, content, course,
                            # email, experiment, live, notification, partner, payment,
                            # program, reel, serie, social, topic, type, user)

# Local testing
npm run shell               # Firebase emulator shell (also: npm run start)

# Releases
npm run release             # Release new version via release-it
```

There is no automated test runner. Testing is done manually via the Firebase emulator shell using mock data in `functions/test/mocks/`.

## Architecture

This service bridges Firebase Realtime Database to Google Pub/Sub. When database paths change, trigger functions publish structured events to Pub/Sub topics for downstream consumers.

**Data flow:**
```
Firebase Realtime Database change
  → Trigger handler (functions/src/triggers/<domain>/)
    → Context creation (transaction ID for tracing)
    → Payload construction (only relevant/changed fields)
    → pubsub.publish(topic, eventName, payload, context)
      → Google Cloud Pub/Sub topic
```

**Source layout:**
- `functions/src/index.js` — entry point with `onInit`
- `functions/src/config.js` — runtime options (512MB, 180s timeout, us-central1), Pub/Sub topic names, log level
- `functions/src/context.js` — per-invocation Context class (UUID transaction ID + function name)
- `functions/src/logger.js` — Google Cloud Logging wrapper (never use `console.log`)
- `functions/src/services/firebase.js` — Firebase Admin SDK init and database access
- `functions/src/services/pubsub.js` — Pub/Sub publisher with cached clients
- `functions/src/triggers/` — one subdirectory per domain, aggregated in `triggers/index.js`

**Pub/Sub topics:** coaching, content, email, experiment, notification, payment, social, user

## Trigger Pattern

Every trigger follows this exact structure:

```js
import { onValueCreated } from 'firebase-functions/v2/database';
import { config } from '../../config.js';
import { Context } from '../../context.js';
import { logger } from '../../logger.js';
import { pubsub } from '../../services/pubsub.js';

const { runtimeOptions, topics } = config;

export const onFooCreated = onValueCreated(
  { ...runtimeOptions, ref: '/foos/{fooId}' },
  async (event) => {
    const context = new Context('onFooCreated');
    const { fooId } = event.params;
    const data = event.data.val();

    if (!data) {
      logger.warn({ context, message: 'Missing data' });
      return;
    }

    const { field1, field2 } = data;
    const payload = { fooId };
    if (field1 != null) payload.field1 = field1;
    if (field2 != null) payload.field2 = field2;

    await pubsub.publish(topics.foo, 'FooCreated', payload, { context });
    logger.info({ context, message: 'Published FooCreated' });
  }
);
```

Key rules:
- Create `new Context('functionName')` at the top of every handler — never share contexts between invocations
- Destructure `config` at module scope, not inside handlers
- Guard against null/missing event data before any service calls
- Only include non-null fields in the payload
- Always include `at` timestamp in payloads (`at: event.time` or similar)
- Use `logger` for all logging — `console.log` is banned by ESLint
- Pass `{context}` to all service calls and logger calls
- Use `onValueCreated`, `onValueWritten`, or `onValueDeleted` from `firebase-functions/v2/database`
- Event names are PascalCase (e.g., `UserCreated`, `ContentUpdated`)

## Adding a New Trigger

1. Create `functions/src/triggers/<domain>/on<Event>.js`
2. Export it from `functions/src/triggers/<domain>/index.js`
3. Export the domain index from `functions/src/triggers/index.js`
4. Add the export name to the `deploy:<domain>` script in `functions/package.json`

## Code Style

ESLint (flat config, ESLint 9+) enforces:
- Single quotes
- `1tbs` brace style (no single-line blocks)
- No `console` statements
- No leading/trailing underscores in identifiers
- Object keys sorted alphabetically (`sort-keys` rule)
- No spaces inside object braces: `{a: 1}` not `{ a: 1 }`

Run `npm run lint` from `functions/` and fix all errors before committing.
