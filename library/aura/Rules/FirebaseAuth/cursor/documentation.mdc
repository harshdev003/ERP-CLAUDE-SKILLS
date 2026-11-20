---
description: Documentation standards for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Documentation

## Code Documentation

- Use JSDoc `@module` tag at the top of every file with a brief `@description`.
- Include `@copyright Aura Health, Inc.` in every module header.
- Use `@see` links to reference Firebase documentation where relevant.
- Document complex business logic inline.

```javascript
/**
 * @module onReelCreated
 * @description Listens for new reels and publishes a ReelCreated event to Pub/Sub.
 * @see https://firebase.google.com/docs/functions/database-events
 * @copyright Aura Health, Inc.
 */
```

## README Standards

### README Should Include:
- Function purpose and overview
- Environment variables and configuration
- Pub/Sub topics consumed or published
- Trigger paths and event types
- Local development setup (`nvm use`, `npm install`, `npm start`)
- Deployment instructions

### README Should NOT Include:
- Internal implementation details
- Pub/Sub payload schemas (keep in the service that owns the event)
- Credentials or secrets

## Trigger Documentation

Each trigger file should have a JSDoc comment describing what event it listens to and what action it takes:

```javascript
/**
 * @description Listens for new reels created at /reels/{reelId}
 * and publishes a ReelCreated event to the content Pub/Sub topic.
 */
const onReelCreated = onValueCreated(...);
```
