# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Commands

All commands run from the `functions/` directory unless noted.

```bash
# Switch to correct Node version first (always required)
nvm use

# Lint (run after every code change; fix all errors before proceeding)
npm run lint
npm run lint:cache   # CI variant with caching

# Deploy
npm run deploy              # all functions
npm run deploy:dev          # dev environment
npm run deploy:prod         # prod environment
npm run deploy:user         # individual domain (replace 'user' with any domain)

# Development
npm run start               # Firebase shell
npm run logs                # View function logs

# Release & maintenance
npm run release             # Version bump + release (uses release-it)
npm run update              # Update all dependencies
```

## Architecture

This project is a **Firebase Cloud Functions v2 event consumer** for the Aura Health platform. It subscribes to domain-specific Pub/Sub topics and writes to Firebase Realtime Database.

**Data flow:**
```
Pub/Sub Topic → onMessagePublished trigger → Firebase Realtime Database transaction
```

**Key layers (all under `functions/src/`):**

- `index.js` — Entry point; uses `onInit()` for cold-start Firebase initialization; exports all triggers
- `config.js` — Runtime options (memory: 512MiB, timeout: 180s, region: us-central1), ignore-origins list
- `context.js` — Per-invocation `Context` class that generates a UUID transaction ID for tracing
- `logger.js` — Google Cloud Logging wrapper (RFC5424 levels); **never use `console.log`**
- `services/firebase.js` — Firebase Admin SDK singleton, exposes `getDatabase()`
- `triggers/{domain}/` — One consumer per domain (15 total: business, challenge, coaching, community, content, course, email, event, experiment, intelligence, notification, payment, program, serie, user)

**Consumer trigger pattern** (follow this exactly for new triggers):

```js
const {onMessagePublished} = require('firebase-functions/v2/pubsub');
const config = require('../../config');
const Context = require('../../context');
const logger = require('../../logger');
const {getDatabase} = require('../../services/firebase');

// Destructure config at module scope
const {events: {ignoreOrigins}, runtime: {options, region}} = config;

const fooConsumer = onMessagePublished({topic: 'foo', ...options, region}, async (event) => {
  const context = new Context('fooConsumer');       // new Context per invocation
  let meta = context.meta();

  const {id: messageId, data: buffer, attributes: {event: eventName}} = event.data.message;
  meta = {...meta, eventName, messageId};
  const messageBody = buffer ? Buffer.from(buffer, 'base64').toString() : null;
  if (!messageBody) { logger.warn('No message body', meta); return; }  // guard clauses first

  const data = JSON.parse(messageBody);
  const {origin} = data;
  if (origin && ignoreOrigins.includes(origin)) { logger.debug('Ignoring origin', meta); return; }

  switch (eventName) {
    case 'FooDone': {
      // read → guard → transaction
      const snapshot = await getDatabase().ref(`/foo/${data.id}`).once('value');
      if (!snapshot.val()) { logger.warn('Not found', meta); break; }
      await getDatabase().ref(`/foo/${data.id}`).transaction((current) => {
        if (current) return {...current, updated: true};
        return null;
      });
      break;
    }
    default:
      logger.info(`Nothing to do for '${eventName}'`, meta);
  }
});

module.exports = fooConsumer;
```

## Adding a New Consumer Function

1. Create `functions/src/triggers/{domain}/{domain}Consumer.js` following the pattern above.
2. Export it from `functions/src/triggers/{domain}/index.js`.
3. Re-export it from `functions/src/triggers/index.js`.
4. Add deploy script to `functions/package.json`:
   ```json
   "deploy:{domain}": "firebase deploy --only functions:{domain}Consumer"
   ```

## Code Rules

- **Origin filtering**: Ignore messages where `origin` is in `config.events.ignoreOrigins` (e.g., `firebase-producer`).
- **Firebase writes**: Always guard against `undefined`; use transactions with a `current` null-check.
- **Timestamps**: Pub/Sub payloads must include `at` (ISO string), fallback: `new Date().toISOString()`.
- **Event names**: PascalCase (e.g., `UserUpdated`, `ReelCreated`).
- **Error handling**: Wrap all I/O in `try/catch`, log with `context.meta()`, re-throw.
- **Logging**: `logger.info()` when a trigger fires, `logger.debug()` for step traces, `logger.warn()` for skipped/not-found.

## Git & Branching

- Branch prefixes: `feature/`, `bugfix/`, `refactor/`, `quality/`
- Conventional commits with scope: `feat(reel): add onReelUpdated trigger` (first line ≤50 chars)
- CI runs lint on CircleCI; all checks must pass before merging

## Environments

- **Dev**: Firebase project `aura-dev`
- **Prod**: Firebase project `firebase-auratech16`
- Always deploy to dev and verify before deploying to prod.
