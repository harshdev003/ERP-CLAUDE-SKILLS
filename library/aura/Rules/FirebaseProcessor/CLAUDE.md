# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Project Overview

Firebase Cloud Functions processor for the Aura platform. Listens for Firebase Realtime Database events and publishes to downstream systems via Pub/Sub. Uses Firebase Functions v2 with Node.js 24.

## Commands

All commands run from `functions/` unless noted.

```bash
nvm use                  # Activate Node 24.15.0 (ALWAYS do this first)
npm run lint             # ESLint (must pass before any PR/deploy)
npm run lint:cache       # ESLint with cache (faster)
npm run deploy:dev       # Deploy to dev (switches firebase project + deploys)
npm run deploy:prod      # Deploy to prod
npm run update           # Update all packages
npm run release          # Release new version (semantic, tags git, GitHub release)
```

CI runs `npm run lint:cache` — linting is the test gate.

## Architecture

**Layered event-driven architecture:**

```
Firebase Realtime DB events
  → Triggers (src/triggers/)       — event handlers, one per entity action
  → Services (src/services/)       — stateless I/O modules (firebase, pubsub, etc.)
  → External (Firebase DB, Pub/Sub, etc.)
```

**Core modules:**

- `src/index.js` — entry point; uses `onInit()` for cold-start service initialization, spreads all trigger exports
- `src/context.js` — per-invocation `Context` class with `functionName` + `transaction` UUID; `context.meta(options)` adds structured metadata to log entries. **Always create a new instance per invocation.**
- `src/logger.js` — Google Cloud Logging wrapper; severity levels 100–600; default level 400 (WARN) suppresses debug/info in prod
- `src/config.js` — centralized runtime config: `functionOption` (memory 512MiB, timeout 180s, region us-central1)
- `src/services/firebase.js` — Firebase Admin SDK; exposes `initialize()` for `onInit` and `getDatabase()`
- `src/triggers/index.js` — aggregates and re-exports all triggers (currently empty)

**Firebase environments (`.firebaserc`):**
- `dev` / `default` → `aura-dev`
- `prod` → `firebase-auratech16`

## Trigger Pattern

Every trigger must follow this exact structure:

```javascript
const {onValueCreated} = require('firebase-functions/v2/database');
const Context = require('../context');
const logger = require('../logger');
const {functionOption} = require('../config');

const onMyEntityCreated = onValueCreated(
  {ref: '/path/{id}', ...functionOption},
  async (event) => {
    const context = new Context('onMyEntityCreated');
    const {id} = event.params;

    try {
      const data = event.data.val();
      if (!data) return;  // guard clause before any I/O

      logger.info(`Processing ${id}`, context.meta({id}));
      await someService.doWork(data, {context});
    } catch (error) {
      logger.error('Failed', context.meta({id, error: error.message}));
      throw error;  // re-throw for Firebase retry
    }
  }
);

module.exports = {onMyEntityCreated};
```

Triggers are organized by domain: `src/triggers/{domain}/on{Entity}{Action}.js`

## Code Style

ESLint enforces these non-obvious rules (see `functions/eslint.config.mjs`):
- `sort-keys: error` — object keys must be sorted alphabetically
- `no-console: error` — use `logger` service, never `console.log`
- `quotes: 'single'` — single quotes only
- `no-underscore-dangle: error` — no `_private` naming

## Deployment

When adding a new trigger, also update `functions/package.json` with the function name (required for Firebase to discover it).

Deploy sequence: dev first, verify, then prod. CircleCI auto-deploys dev on version tags; prod promotion is manual.
