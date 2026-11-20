# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Node.js Version

Always run `nvm use` before any commands. The project requires Node 24.15.0 (see `.nvmrc`).

## Commands

All commands run from `functions/`:

```bash
npm run lint          # ESLint — must pass before any PR
npm run lint:cache    # ESLint with caching
npm run deploy:dev    # Deploy to dev (aura-dev)
npm run deploy:prod   # Deploy to prod (firebase-auratech16) — only after dev verification
npm run deploy        # Deploy all functions
```

No test runner is configured. A `test/` directory with mocks exists but has no active scripts.

## Architecture

Firebase Cloud Functions v2 (event-driven) project. All application code lives under `functions/src/`.

**Layer structure:**
- `src/triggers/{domain}/on{Entity}{Action}.js` — Firebase event handlers
- `src/services/` — External integrations (firebase admin, pubsub, etc.) — stateless exported functions
- `src/config.js` — Centralized runtime options, logger level, topics, region
- `src/context.js` — Per-invocation `Context` class (transaction UUID + functionName for tracing)
- `src/logger.js` — Google Cloud Logging wrapper (debug/info/warn/error/crit)
- `src/index.js` — Entry point: `onInit` initializes services, spreads all triggers into `module.exports`

**Trigger discovery chain:** `index.js` → `triggers/index.js` → `triggers/{domain}/index.js` → individual trigger files

**Currently deployed function:** `processActivityTask` — listens on `/queue/activities/tasks/{taskId}` (Realtime Database `onValueCreated`), processes social activity events (like, comment, post, likeComment, follow, postMeditation), fans out to receivers, writes activity records, and sends push notifications.

## Trigger Pattern

Every trigger must follow this pattern:

```javascript
const {options, region} = config.runtime; // destructured at module scope

const onReelCreated = onValueCreated(
  {ref: '/reels/{reelId}', ...options, region},
  async (event) => {
    const context = new Context('onReelCreated'); // new Context per invocation
    const {reelId} = event.params;
    const reel = event.data.val();
    if (!reel) return; // guard clause before any service calls

    try {
      logger.info(`Reel '${reelId}'`, context.meta({reelId}));
      await someService.call(data, {context}); // pass {context} to all service calls
    } catch (error) {
      logger.error('Failed', context.meta({reelId, error: error.message}));
      throw error; // always re-throw so Firebase can retry
    }
  },
);
```

## Logging

Default `config.logger.level` is `400` (WARNING) — `debug` and `info` are suppressed in production unless lowered.

| Method | Severity | Use for |
|--------|----------|---------|
| `logger.debug()` | 100 | Step-by-step traces, variable values |
| `logger.info()` | 200 | Trigger fired, successful operations |
| `logger.warn()` | 400 | Missing optional data, fallback scenarios |
| `logger.error()` | 500 | Failed operations, caught exceptions |
| `logger.crit()` | 600 | Critical failures |

Always pass `context.meta({...fields})` as the second argument to every log call.

## Adding a New Trigger

1. Create `src/triggers/{domain}/on{Entity}{Action}.js`
2. Export from `src/triggers/{domain}/index.js`
3. Export from `src/triggers/index.js`
4. **Update `functions/package.json`** — append the function name to the `--only functions:` list in the relevant `deploy:<domain>` script (this is mandatory, not optional)

## Config

Destructure at module scope, not inside handlers:

```javascript
const {runtime: {options, region}} = config;
```

Use `process.env` for secrets/credentials. Use `config.js` constants for runtime options, topic names, validation lists, and region.

## Naming Conventions

- Files and functions: `camelCase`
- Constants: `UPPER_SNAKE_CASE`
- Classes: `PascalCase`
- Trigger functions: `on{Entity}{Action}` (e.g., `onReelCreated`, `onUserDeleted`)
- Domain folders: `camelCase` (e.g., `reel/`, `user/`)
- The exported function name is the deployed Firebase function name — keep them in sync

## Firebase / Database

- Never import `firebase-admin` directly in trigger files — use `services/firebase.js`
- Always guard against `null` from `event.data.val()` (deleted nodes return `null`)
- Never write `undefined` to Firebase paths
- Use `new Date().toISOString()` for timestamps

## Git Workflow

Branch naming: `feature/`, `bugfix/`, `refactor/`, `quality/` prefixes.
Commits: conventional commits format, scope when relevant (e.g., `feat(queue): ...`), first line under 50 chars.
