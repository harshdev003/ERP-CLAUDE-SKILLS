# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Project Overview

Firebase Cloud Functions (v2) that automatically fix malformed data written to Firebase Realtime Database. Functions are organized by domain (coach, content, user) and listen to specific database paths to apply corrective transformations.

## Commands

All commands run from the `functions/` directory:

```bash
nvm use                    # Required before any changes
npm run lint               # Required after any changes
npm run lint:cache         # Lint with caching
npm run deploy             # Deploy all functions
npm run deploy:dev         # Deploy to dev environment
npm run deploy:prod        # Deploy to prod environment
npm run deploy:coach       # Deploy coach functions only
npm run deploy:content     # Deploy content functions only
npm run deploy:user        # Deploy user functions only
npm run shell              # Firebase shell for interactive testing
npm run logs               # View project logs
npm run release            # Release a new version
```

Deploy a single function:
```bash
firebase deploy --only functions:fixUserGivenName --non-interactive
```

## Architecture

### Directory Structure

```
functions/src/
├── index.js           # Entry point: onInit + spread all triggers into exports
├── context.js         # Per-invocation Context class (UUID tracing, function name)
├── config.js          # Runtime config (memory, timeout, region, logger level)
├── logger.js          # Google Cloud Logging wrapper (debug/info/warn/error/crit)
├── services/
│   └── firebase.js    # Firebase Admin SDK abstraction
└── triggers/
    ├── index.js       # Aggregates and re-exports all domain triggers
    ├── coach/
    ├── content/
    └── user/
```

### Key Patterns

**Trigger structure**: Each trigger creates a new `Context` instance, destructures config options, guards against null data, then calls services with context attached.

**Context**: Stores a UUID transaction ID and function name per invocation; `context.meta()` attaches structured tracing metadata to every log call.

**Entry point**: `onInit()` handles one-time initialization (Firebase Admin setup). All triggers are spread into `module.exports` for Cloud Functions discovery.

**Services**: Stateless modules with an `initialize()` method called during `onInit`.

## Coding Conventions

- Single quotes, trailing commas, 2-space indentation
- Guard clauses for null/missing data before processing
- Always use `logger`, never `console`
- Always pass `context.meta()` to log calls
- Destructure `...options` and region from config into every trigger
- Use `Set`/`Map` over repeated `includes()` checks
- No hardcoded secrets — use environment variables

## Adding New Triggers

1. Create `src/triggers/{domain}/fix{Domain}{Thing}.js`
2. Export from `src/triggers/{domain}/index.js`
3. Export from `src/triggers/index.js`
4. Add the function to the `deploy:{domain}` script in `functions/package.json`
