# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Commands

All commands run from `functions/`:

```bash
npm run lint          # Run ESLint
npm run lint:cache    # Run ESLint with cache
npm run shell         # Start Firebase emulator shell locally
npm run deploy:dev    # Deploy all functions to dev (aura-dev)
npm run deploy:prod   # Deploy all functions to prod (firebase-auratech16)
npm run deploy:social # Deploy only querySortedActivities
npm run deploy:user   # Deploy only querySortedEmail and querySortedSkips
npm run logs          # View Firebase function logs
npm run release       # Cut a new release with release-it
```

There are no automated tests — CI runs `npm run lint:cache` only.

## Architecture

Firebase Cloud Functions (v2) project with scheduled triggers that query Firebase Realtime Database and log structured results to Google Cloud Logging.

**Scheduled triggers** (`functions/src/triggers/`) run every 1 minute:
- `user/querySortedEmail` — queries users by email, logs the key found
- `user/querySortedSkips` — queries skips by a user over the past 10 days, logs count
- `social/querySortedActivities` — queries activities received by a user (limitToLast 20), logs count

**Request tracing**: Each function invocation creates a `Context` instance (`src/context.js`) that generates a UUID and holds the function name. This context flows through to the logger and firebase service, attaching `{ functionName, transactionId }` metadata to every log entry.

**Logging** (`src/logger.js`): Wraps `@google-cloud/logging` with five severity levels (DEBUG/INFO/WARNING/ERROR/CRITICAL). Minimum log level is WARNING (400). Each entry includes service name, version, region, function name, and transaction ID.

**Firebase service** (`src/services/firebase.js`): Thin wrapper around Firebase Admin SDK. The `db` instance is initialized once at startup in `src/index.js` via `getDatabase()` and passed into service calls.

**Config** (`src/config.js`): Derives service name from `package.json`, sets region (`us-central1`), memory (`512MiB`), and timeout (`180s`).

## Deployment Pipeline

Tags matching `v*` trigger CircleCI: lint → deploy to dev → promote to prod. Releases are created via `npm run release` (uses `release-it`, no npm publish).

## Code Style

- CommonJS (`require`/`module.exports`)
- ESLint v9 flat config — single quotes, no console, alphabetically sorted object keys, 1tbs brace style
- JSDoc on all modules and exported functions
