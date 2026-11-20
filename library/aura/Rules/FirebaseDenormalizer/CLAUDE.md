# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Setup

Always run `nvm use` before making changes (enforces Node 24.15.0).

## Commands

All commands run from `functions/`:

```bash
npm run lint          # ESLint — must pass after every change
npm run lint:cache    # ESLint with caching (faster)
npm run deploy:dev    # Deploy all functions to dev
npm run deploy:prod   # Deploy all functions to prod
npm run deploy:<domain>  # Deploy a specific domain (e.g. deploy:user, deploy:coach)
npm run shell         # Firebase local shell
npm run logs          # Display Firebase function logs
```

## Architecture

Firebase Cloud Functions v2 denormalization engine. Listens to Realtime Database events and propagates denormalized data across the system. 135+ Cloud Functions organized by domain.

### Layers

```
functions/src/
├── index.js          # Entry point — onInit + module.exports spread of all triggers
├── config.js         # Runtime config (512MiB, 180s timeout, us-central1)
├── context.js        # Per-invocation request tracking (new Context per trigger)
├── logger.js         # Google Cloud Logging wrapper (no console.log)
├── services/         # External integrations (Firebase Admin)
├── triggers/         # 18 domain folders with event handlers
│   └── {domain}/on{Entity}{Action}.js
└── models/, utils/
```

### Trigger pattern

Every trigger file follows this structure:

```javascript
const context = new Context('onReelCreated');
const {reelId} = event.params;
const meta = context.meta({reelId});
logger.info(`Reel '${reelId}'`, meta);
// ... guard against null event.data.val() before service calls
// ... wrap I/O in try/catch, log with context.meta(), re-throw for Firebase retry
```

- File naming: `on{Entity}{Action}.js` (e.g. `onReelCreated.js`)
- Always create a new `Context` per invocation — never share between triggers
- Pass `{context}` to every service call
- Guard against null `event.data.val()` before any service calls
- Re-throw errors after logging so Firebase can retry

### Adding a trigger

1. Create `functions/src/triggers/{domain}/on{Entity}{Action}.js`
2. Export it from `functions/src/triggers/{domain}/index.js`
3. Add the function name to the `deploy:{domain}` script in `functions/package.json`

### Logging

Use `logger` only — no `console.log()`. Always pass `context.meta()` for structured logs.

Log levels: debug (100), info (200), warn (400), error (500), crit (600). Production logger level is 400 (WARNING), so debug/info are suppressed in prod.

## Code Style

ESLint flat config enforces: single quotes, trailing commas, 2-space indentation, no console, sorted object keys, 1TBS brace style. Run `npm run lint` to verify.

## Deployment

Deploy to dev first, verify, then deploy to prod. Use domain-specific deploy scripts for targeted deployments rather than deploying all functions at once.
