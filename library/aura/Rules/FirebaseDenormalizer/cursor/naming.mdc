---
description: Naming conventions for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Naming Conventions

## General Naming

- **Files**: `camelCase.js` (e.g., `onReelCreated.js`, `pubsub.js`)
- **Functions**: `camelCase` (e.g., `onReelCreated`, `publishEvent`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `CONTENT_TOPIC`, `MAX_RETRY`)
- **Classes**: `PascalCase` (e.g., `Context`)
- **Variables**: `camelCase` (e.g., `reelId`, `eventData`)

## Trigger Naming

Trigger functions follow the `on{Entity}{Action}` pattern:

| Pattern | Example |
|---------|---------|
| `on{Entity}Created` | `onReelCreated`, `onUserCreated` |
| `on{Entity}Updated` | `onCoachUpdated`, `onContentUpdated` |
| `on{Entity}Deleted` | `onProgramDeleted`, `onUserDeleted` |
| `on{Entity}{SpecificAction}` | `onContentFavorited`, `onUserLoggedIn` |

- Trigger function names must exactly match their exported names in `module.exports` — Firebase uses the export name as the deployed function name.
- File names match the exported function name: `onReelCreated.js` exports `onReelCreated`.

## Domain Folders

Triggers are grouped by domain under `src/triggers/{domain}/`:

- `camelCase` folder names (e.g., `reel/`, `user/`, `payment/`, `coach/`)
- Each domain folder has an `index.js` that re-exports all triggers in that domain

## Service Files

- `src/services/{name}.js` — camelCase, describes the external system (e.g., `firebase.js`, `pubsub.js`, `mixpanel.js`)

## Pub/Sub Event Names

- Use `PascalCase` with entity-action format: `ReelCreated`, `UserDeleted`, `PaymentCompleted`
- Event names must be consistent with the consuming function's expected event name
